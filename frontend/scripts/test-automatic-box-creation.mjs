import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { appHarness } from './app-operation-test-harness.mjs';
import { createBoxEnvironment, createBoxHarness, createPilotageHarness, plain, text } from './create-box-harness.mjs';

const complete = { status: 'usable', prefix: 'AUR-ATL-1', species_code: 'AUR', origin_code: 'ATL', strain_number: '1', recorded_provenance_name: null };
const strainOption = (id = 1, overrides = {}) => ({
  id, species: { id: 101, scientific_name: 'Aurelia aurita' }, name: '', code: 'AUR-ATL-1',
  active_box_count: 2, identity: complete, ...overrides,
});
const options = [strainOption()];
const STORAGE = 'polypbase.automaticBox.7.1';
const save = h => h.panel.byClass('create-box-submit');
const option = (h, id = 1) => h.panel.nodes.find(n => n.props.role === 'option' && n.props.id === `create-box-strain-option-${id}`);
const field = (h, name) => h.panel.field(`create-box-${name}-field`);
const notes = h => h.panel.nodes.find(n => n.type === 'textarea');
// Built nodes include nested leaf output that the JSX value does not.
const panelText = h => h.panel.nodes.map(n => [].concat(n.props.children ?? []).filter(c => typeof c === 'string' || typeof c === 'number').join('')).join('');
const stored = h => JSON.parse(h.env.window.sessionStorage.getItem(STORAGE));
const committed = { id: 42, global_code: 'AUR-ATL-1.007', box_number: '007', strain: { id: 1 }, species: { scientific_name: 'Aurelia aurita' } };
const TECHNICAL_COPY = /createBoxPendingIntent|createBoxResolve|createBoxRequest|Résoudre|Resolve request|request_id|doublon|duplicate/i;

async function ready(h = createBoxHarness(), strains = options) {
  h.env.requests.at(-1).resolve(strains);
  await h.panel.settle();
  h.panel.click(option(h));
  if (!field(h, 'location').disabled) h.panel.change(field(h, 'location'), '10');
  return h;
}
async function send(h) {
  const result = h.panel.submit();
  await h.panel.settle();
  return { result };
}
function failure(h, kind) {
  if (kind === 'network') return new TypeError('Failed to fetch');
  if (kind === 'syntax') return new SyntaxError('Unexpected end of JSON input');
  return new h.api.ApiError(Number(kind), 'Server failure');
}

test('form asks only for strain, location, date and note; no preview or client-allocated number', async () => {
  const h = createBoxHarness();
  assert.equal(h.env.requests[0].path, '/api/boxes/automatic/strains/');
  await ready(h);
  assert.equal(h.boxCollectionRequests, 0);
  const controls = h.panel.nodes.filter(n => ['select', 'input', 'textarea'].includes(n.type));
  assert.deepEqual(controls.map(n => n.props.type ?? n.type), ['search', 'select', 'date', 'textarea']);
  assert.equal(h.panel.byClass('created-box-identifier'), undefined);
  assert.equal(h.env.requests.length, 1, 'Only the strain list is fetched');
  assert.doesNotMatch(panelText(h), /Identifiant prévu|Expected identifier|AUR-ATL-1\.\d/);
  h.panel.change(field(h, 'date'), '');
  assert.equal(save(h).disabled, true);
  await h.panel.submit();
  assert.equal(h.mutations.length, 0);
  h.panel.change(field(h, 'date'), '2026-10-01');
  await send(h);
  assert.equal(h.confirmations.length, 0, 'Creation does not require confirmation');
  assert.equal(h.mutations.length, 1);
  h.unmount();
});

test('no strain or location is preselected; an unusable identity cannot be submitted', async () => {
  const h = createBoxHarness();
  h.env.requests[0].resolve([strainOption(), strainOption(2, { code: '1-LAB', identity: { status: 'unusable', reason: 'code_format' } })]);
  await h.panel.settle();
  assert.equal(h.panel.nodes.some(n => n.props['aria-selected'] === true), false);
  assert.equal(field(h, 'location').props.value, '');
  assert.equal(save(h).disabled, true);
  h.panel.change(field(h, 'location'), '10');
  assert.equal(option(h, 2), undefined, 'Unusable identities are absent from the picker');
  assert.equal(save(h).disabled, true);
  assert.equal(h.panel.byClass('created-box-identifier'), undefined);
  assert.equal(h.env.requests.length, 1, 'Only the strain list is fetched');
  await h.panel.submit();
  assert.equal(h.mutations.length, 0);
  h.panel.click(option(h, 1));
  assert.equal(save(h).disabled, false);
  h.unmount();
});

test('synchronous double submit sends exactly one UUID intent, persisted before the request', async () => {
  const h = await ready();
  h.panel.change(field(h, 'date'), '2026-10-01');
  h.panel.change(notes(h), ' note ');
  const first = h.panel.submit();
  await h.panel.submit();
  await h.panel.settle();
  assert.equal(h.mutations.length, 1);
  const payload = plain(h.mutations[0].payload);
  assert.match(payload.request_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(payload, { strain: 1, thermal_zone: 10, entered_on: '2026-10-01', volume_liters: null, notes: 'note', request_id: payload.request_id, resume_token: payload.resume_token });
  assert.match(payload.resume_token, /^[0-9a-f-]{36}$/);
  assert.notEqual(payload.request_id, payload.resume_token);
  const { digest, ...persisted } = stored(h);
  assert.match(digest, /^[0-9a-f]{64}$/);
  assert.deepEqual(persisted, { ...payload, version: 2, scope: STORAGE });
  assert.equal(save(h).disabled, true);
  assert.equal(text(save(h)), 'createBoxCreating');
  assert.equal(field(h, 'date').disabled, true);
  await h.panel.submit();
  assert.equal(h.mutations.length, 1);
  h.mutations[0].resolve(committed);
  await first;
  h.unmount();
});

for (const presentation of ['modal', 'inline']) test(`${presentation}: server code is shown, storage settled, then the box opens on request`, async () => {
  const h = createBoxHarness({ presentation });
  if (presentation === 'inline') h.panel.click(h.panel.byClass('create-box-toggle'));
  await ready(h);
  const target = h.env.external('section', { 'data-box-page-focus-target': true, tabIndex: -1 });
  const { result } = await send(h);
  h.mutations[0].resolve(committed);
  await result;
  h.panel.flush();
  assert.ok(panelText(h).includes('createBoxSuccess'));
  assert.ok(panelText(h).includes(committed.global_code));
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), null);
  assert.deepEqual(h.navigations, []);
  if (presentation === 'modal') {
    assert.equal(h.env.document.activeElement, h.panel.byClass('create-box-open-created'));
    h.panel.setProps({ isOpen: false });
    h.panel.setProps({ isOpen: true });
  } else {
    h.panel.click(h.panel.byClass('create-box-toggle'));
    h.panel.click(h.panel.byClass('create-box-toggle'));
  }
  assert.ok(panelText(h).includes(committed.global_code));
  h.panel.click(h.panel.byClass('create-box-open-created'));
  assert.deepEqual(h.navigations, [42]);
  h.unmount();
  assert.equal(h.env.frameCount, presentation === 'modal' ? 1 : 0);
  h.env.flushFrames();
  if (presentation === 'modal') assert.equal(h.env.document.activeElement, target);
});

test('creating another box keeps strain, location and date, clears the note and uses a new UUID', async () => {
  const h = await ready();
  h.panel.change(field(h, 'date'), '2026-09-09');
  h.panel.change(notes(h), 'First');
  const { result } = await send(h);
  const firstId = h.mutations[0].payload.request_id;
  h.mutations[0].resolve(committed);
  await result;
  h.panel.flush();
  h.panel.click(h.panel.byClass('create-box-another'));
  assert.equal(option(h).props['aria-selected'], true);
  assert.equal(field(h, 'location').props.value, 10);
  assert.equal(field(h, 'date').props.value, '2026-09-09');
  assert.equal(notes(h).props.value, '');
  await send(h);
  assert.notEqual(h.mutations[1].payload.request_id, firstId);
  h.unmount();
});

for (const kind of ['network', 'syntax', '500', '502', '408', '429']) test(`${kind}: uncertain outcome replays the same intent automatically, without technical copy`, async () => {
  const h = await ready();
  const { result } = await send(h);
  const payload = plain(h.mutations[0].payload);
  h.mutations[0].reject(failure(h, kind));
  await result;
  h.panel.flush();
  assert.equal(field(h, 'date').disabled, true);
  assert.equal(notes(h).disabled, true);
  assert.ok(panelText(h).includes('createBoxConnectionPending'));
  assert.doesNotMatch(panelText(h), TECHNICAL_COPY);
  assert.equal(h.panel.byClass('create-box-retry-now'), undefined, 'No manual action during automatic retries');
  assert.equal(h.resolutions.length, 0, 'An uncertain outcome is replayed, never cancelled');
  assert.deepEqual(h.env.timerDelays, [1000]);
  h.env.runTimers();
  await h.panel.settle();
  assert.equal(h.mutations.length, 2);
  assert.deepEqual(plain(h.mutations[1].payload), payload);
  h.mutations[1].resolve(committed);
  await h.panel.settle();
  assert.ok(panelText(h).includes(committed.global_code));
  assert.equal(h.env.timerDelays.length, 0);
  h.unmount();
});

test('retries are bounded; reconnection or retry restarts them with the same key', async () => {
  const h = await ready();
  const { result } = await send(h);
  const id = h.mutations[0].payload.request_id;
  h.mutations[0].reject(failure(h, 'network'));
  await result;
  const delays = [];
  for (let attempt = 1; attempt <= 6; attempt++) {
    delays.push(...h.env.timerDelays);
    h.env.runTimers();
    await h.panel.settle();
    h.mutations.at(-1).reject(failure(h, 'network'));
    await h.panel.settle();
  }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 15000, 30000]);
  assert.equal(h.mutations.length, 7);
  assert.deepEqual(h.env.timerDelays, [], 'No unbounded retry loop');
  assert.ok(h.panel.byClass('create-box-retry-now'));
  h.env.dispatch('online');
  await h.panel.settle();
  assert.equal(h.mutations.length, 8);
  assert.equal(h.mutations[7].payload.request_id, id);
  h.mutations[7].reject(failure(h, '503'));
  await h.panel.settle();
  assert.deepEqual(h.env.timerDelays, [1000], 'Reconnection restarts the bounded schedule');
  h.panel.setProps({ isOpen: false });
  h.panel.setProps({ isOpen: true });
  await h.panel.settle();
  assert.equal(h.mutations.length, 9, 'Reopening the dialog retries immediately');
  assert.equal(h.mutations[8].payload.request_id, id);
  h.mutations[8].resolve(committed);
  await h.panel.settle();
  assert.ok(panelText(h).includes(committed.global_code));
  h.unmount();
});

for (const [body, expected] of [
  [{ strain: ['Recorded code cannot be used.'] }, 'Recorded code cannot be used.'],
  [{ thermal_zone: ['Select an active zone.'] }, 'Select an active zone.'],
  [{}, 'createBoxNotCreated'],
]) test('a durable rejected/cancelled intent unlocks the unchanged form without UUID-only cancellation', async () => {
  const h = await ready();
  h.panel.change(notes(h), 'Keep my draft');
  h.panel.change(field(h, 'date'), '2026-10-02');
  await send(h);
  const id = h.mutations[0].payload.request_id;
  const message = Object.values(body).flat()[0] ?? '';
  h.mutations[0].reject(new h.api.ApiError(422, message, { ...body, code: 'box_creation_intent_settled' }));
  await h.panel.settle();
  assert.equal(h.resolutions.length, 0);
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), null);
  assert.equal(field(h, 'date').disabled, false);
  assert.equal(field(h, 'date').props.value, '2026-10-02');
  assert.equal(notes(h).props.value, 'Keep my draft');
  assert.ok(panelText(h).includes(expected));
  assert.doesNotMatch(panelText(h), TECHNICAL_COPY);
  assert.equal(h.mutations.length, 1);
  await send(h);
  assert.notEqual(h.mutations[1].payload.request_id, id);
  h.unmount();
});

for (const status of [400, 403, 409]) test(`nonterminal ${status} never authorizes cancellation or replacement`, async () => {
  const h = await ready();
  await send(h);
  const kept = h.env.window.sessionStorage.getItem(STORAGE);
  h.mutations[0].reject(new h.api.ApiError(status, 'Refused', { code: 'box_creation_request_conflict' }));
  await h.panel.settle();
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), kept);
  assert.equal(field(h, 'date').disabled, true);
  assert.equal(h.resolutions.length, 0);
  assert.deepEqual(h.env.timerDelays, []);
  assert.ok(panelText(h).includes(status === 403 ? 'createBoxForbidden' : 'createBoxPreviousUnverified'));
  h.unmount();
});

test('reload resumes the same complete token-bound intention, including when registration never arrived', async () => {
  const h = await ready();
  const { result } = await send(h);
  const payload = plain(h.mutations[0].payload);
  h.mutations[0].reject(failure(h, 'network'));
  await result;
  h.unmount();
  assert.deepEqual(h.env.timerDelays.length, 0, 'Unmount clears the retry timer');
  assert.equal(h.env.windowListenerCount('online'), 0);
  const restored = createBoxHarness({ isOpen: false }, { environment: h.env });
  await restored.panel.settle();
  assert.equal(restored.mutations.length, 1);
  assert.equal(restored.resolutions.length, 0);
  assert.deepEqual(plain(restored.mutations[0].payload), payload);
  restored.mutations[0].resolve(committed);
  await restored.panel.settle();
  restored.panel.setProps({ isOpen: true });
  assert.ok(panelText(restored).includes(committed.global_code));
  assert.equal(restored.env.window.sessionStorage.getItem(STORAGE), null);
  restored.unmount();
});

const KEY = `${STORAGE}.requestId`;
function seeded(entries) {
  const env = createBoxEnvironment();
  for (const [key, value] of Object.entries(entries)) env.window.sessionStorage.setItem(key, value);
  return env;
}
const replayable = id => JSON.stringify({ strain: 1, thermal_zone: 10, entered_on: '2026-10-01', volume_liters: null, notes: '', request_id: id });

test('submission stores the raw request key first, then the envelope, with the same UUID', async () => {
  const h = await ready();
  const writes = [];
  const setItem = h.env.window.sessionStorage.setItem;
  h.env.window.sessionStorage.setItem = (key, value) => { writes.push(key); setItem(key, value); };
  await send(h);
  const id = h.mutations[0].payload.request_id;
  assert.deepEqual(writes, [KEY, STORAGE]);
  assert.equal(h.env.window.sessionStorage.getItem(KEY), id);
  assert.equal(stored(h).request_id, id);
  h.mutations[0].resolve(committed);
  await h.panel.settle();
  assert.equal(h.env.window.sessionStorage.getItem(KEY), null);
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), null);
  h.unmount();
});

for (const failingKey of ['key', 'envelope']) test(`failure to store the ${failingKey} sends nothing and leaves no half record`, async () => {
  const h = await ready();
  const setItem = h.env.window.sessionStorage.setItem;
  h.env.window.sessionStorage.setItem = (key, value) => {
    if ((failingKey === 'key') === (key === KEY)) throw new Error('Quota');
    setItem(key, value);
  };
  await send(h);
  assert.equal(h.mutations.length, 0);
  assert.equal(h.env.window.sessionStorage.getItem(KEY), null);
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), null);
  assert.ok(panelText(h).includes('createBoxStorageUnavailable'));
  h.unmount();
});

const inspections = h => h.env.requests.filter(r => r.path.startsWith('/api/boxes/automatic/resolve/?'));
const inspectedIds = h => inspections(h).map(r => new URL(r.path, 'https://test.invalid').searchParams.get('request_id'));
async function inspectNext(h, result) {
  inspections(h).at(-1).resolve(result);
  await h.panel.settle();
  assert.equal(h.resolutions.length, 0, 'Stored candidates never reach mutating resolution');
  assert.equal(h.mutations.length, 0, 'Stored candidates never create or replay');
}
function divergent() {
  const envelopeId = crypto.randomUUID(), keyId = crypto.randomUUID();
  return { envelopeId, keyId, env: seeded({ [STORAGE]: replayable(envelopeId), [KEY]: keyId }) };
}
const boxFor = (id, strain = 1) => ({ ...committed, id, global_code: `AUR-ATL-${strain}.${id}`, strain: { id: strain } });

for (const envelope of ['{not json', 'null', '[]', JSON.stringify({ request_id: 'bad' }), JSON.stringify({ strain: 1 })]) {
  test(`corrupt envelope ${envelope}: preserved UUID only inspected, committed box recovered`, async () => {
    const id = crypto.randomUUID();
    const env = seeded({ [STORAGE]: envelope, [KEY]: id });
    const h = createBoxHarness({}, { environment: env });
    await h.panel.settle();
    assert.deepEqual(inspectedIds(h), [id]);
    await inspectNext(h, { status: 'created', box: committed });
    assert.ok(panelText(h).includes(committed.global_code));
    assert.equal(env.window.sessionStorage.getItem(KEY), null);
    assert.doesNotMatch(panelText(h), TECHNICAL_COPY);
    h.unmount();
  });
}

for (const withKey of [true, false]) test(`intact restored envelope (raw key ${withKey}) is inspection-only`, async () => {
  const id = crypto.randomUUID();
  const env = seeded({ [STORAGE]: replayable(id), ...(withKey ? { [KEY]: id } : {}) });
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  assert.deepEqual(inspectedIds(h), [id]);
  await inspectNext(h, { status: 'unknown' });
  assert.equal(field(h, 'date').disabled, true);
  assert.equal(env.window.sessionStorage.getItem(STORAGE), replayable(id));
  h.env.runTimers();
  await h.panel.settle();
  await inspectNext(h, { status: 'created', box: committed });
  assert.ok(panelText(h).includes(committed.global_code));
  h.unmount();
});

test('corrupt envelope, unknown UUID: no cancellation, unlock or replacement across reload', async () => {
  const id = crypto.randomUUID();
  const env = seeded({ [STORAGE]: '{broken', [KEY]: id });
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  await inspectNext(h, { status: 'unknown' });
  await h.panel.submit();
  assert.equal(h.mutations.length, 0);
  assert.equal(field(h, 'date').disabled, true);
  h.unmount();
  const restored = createBoxHarness({}, { environment: env });
  await restored.panel.settle();
  assert.equal(inspectedIds(restored).at(-1), id);
  await inspectNext(restored, { status: 'created', box: committed });
  assert.ok(panelText(restored).includes(committed.global_code));
  restored.unmount();
});

test('an existing durable cancellation permits editing, but inspection never creates one', async () => {
  const id = crypto.randomUUID();
  const h = createBoxHarness({}, { environment: seeded({ [KEY]: id }) });
  await h.panel.settle();
  await inspectNext(h, { status: 'cancelled' });
  assert.equal(field(h, 'date').disabled, false);
  assert.equal(h.env.window.sessionStorage.getItem(KEY), null);
  h.unmount();
});

for (const terminal of ['unknown', 'created']) test(`divergent UUIDs, first ${terminal}: no attribution of independent intentions`, async () => {
  const { env, envelopeId, keyId } = divergent();
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  const a = boxFor(11), b = boxFor(12, 2);
  await inspectNext(h, terminal === 'created' ? { status: 'created', box: a } : { status: 'unknown' });
  await inspectNext(h, { status: 'unknown' });
  assert.deepEqual(inspectedIds(h), [envelopeId, keyId]);
  assert.equal(env.window.sessionStorage.getItem(KEY), keyId);
  if (terminal === 'created') {
    assert.ok(panelText(h).includes(a.global_code), 'Committed evidence stays visible');
    assert.equal(h.panel.byClass('create-box-another').disabled, true);
  } else assert.equal(field(h, 'date').disabled, true);
  h.env.runTimers();
  await h.panel.settle();
  await inspectNext(h, { status: 'created', box: a });
  await inspectNext(h, { status: 'created', box: b });
  assert.ok(panelText(h).includes(a.global_code));
  assert.ok(panelText(h).includes(b.global_code), 'Independent strain and Box evidence is preserved');
  assert.equal(h.panel.byClass('create-box-another').disabled, false);
  assert.equal(env.window.sessionStorage.getItem(KEY), null);
  h.unmount();
});

test('divergence from one original intention: committed plus unknown never means resolved', async () => {
  const { env } = divergent();
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  await inspectNext(h, { status: 'unknown' });
  await inspectNext(h, { status: 'created', box: committed });
  assert.ok(panelText(h).includes(committed.global_code));
  assert.equal(h.panel.byClass('create-box-another').disabled, true);
  assert.ok(env.window.sessionStorage.getItem(KEY));
  assert.doesNotMatch(panelText(h), TECHNICAL_COPY);
  h.unmount();
});

test('temporary inspection failure keeps committed evidence visible, including after reload', async () => {
  const { env, keyId } = divergent();
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  await inspectNext(h, { status: 'created', box: committed });
  inspections(h).at(-1).reject(new TypeError('Failed to fetch'));
  await h.panel.settle();
  assert.ok(panelText(h).includes(committed.global_code));
  assert.ok(panelText(h).includes('createBoxConnectionPending'));
  assert.equal(h.panel.byClass('create-box-another').disabled, true);
  assert.equal(env.window.sessionStorage.getItem(KEY), keyId);
  h.unmount();
  const restored = createBoxHarness({}, { environment: env });
  await restored.panel.settle();
  await inspectNext(restored, { status: 'created', box: committed });
  await inspectNext(restored, { status: 'created', box: boxFor(77, 2) });
  assert.ok(panelText(restored).includes(committed.global_code));
  restored.unmount();
});

test('duplicated tab inspects only and cannot cancel or replay the original in-flight creation', async () => {
  const original = await ready();
  const { result } = await send(original);
  const payload = plain(original.mutations[0].payload);
  const copy = createBoxHarness({}, { environment: seeded({ [STORAGE]: JSON.stringify(payload), [KEY]: payload.request_id }) });
  await copy.panel.settle();
  await inspectNext(copy, { status: 'unknown' });
  assert.equal(field(copy, 'date').disabled, true);
  original.mutations[0].resolve(committed);
  await result;
  copy.env.runTimers();
  await copy.panel.settle();
  await inspectNext(copy, { status: 'created', box: committed });
  assert.ok(panelText(copy).includes(committed.global_code));
  assert.equal(original.mutations.length, 1);
  original.unmount();
  copy.unmount();
});

test('both independently committed candidates are displayed and settled without writes', async () => {
  const { env } = divergent();
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  const a = boxFor(11), b = boxFor(12, 2);
  await inspectNext(h, { status: 'created', box: a });
  await inspectNext(h, { status: 'created', box: b });
  assert.ok(panelText(h).includes(a.global_code));
  assert.ok(panelText(h).includes(b.global_code));
  assert.equal(h.panel.byClass('create-box-another').disabled, false);
  assert.equal(env.window.sessionStorage.getItem(KEY), null);
  h.unmount();
});

test('inspection forbidden keeps the identity and form locked, without falling back to cancellation', async () => {
  const id = crypto.randomUUID();
  const h = createBoxHarness({}, { environment: seeded({ [KEY]: id }) });
  await h.panel.settle();
  inspections(h).at(-1).reject(new h.api.ApiError(403, 'Forbidden'));
  await h.panel.settle();
  assert.ok(panelText(h).includes('createBoxForbidden'));
  assert.equal(field(h, 'date').disabled, true);
  assert.equal(h.env.window.sessionStorage.getItem(KEY), id);
  assert.equal(h.resolutions.length + h.mutations.length, 0);
  h.unmount();
});

test('unreadable storage is not an empty session and cannot enable a replacement create', async () => {
  const env = createBoxEnvironment();
  env.window.sessionStorage.getItem = () => { throw new Error('Unavailable'); };
  const h = createBoxHarness({}, { environment: env });
  await ready(h);
  await send(h);
  assert.ok(panelText(h).includes('createBoxPreviousUnverified'));
  assert.equal(save(h).disabled, true);
  assert.equal(h.resolutions.length + h.mutations.length, 0);
  h.unmount();
});

test('inspection retries are bounded and late replies cannot clear storage after unmount', async () => {
  const id = crypto.randomUUID();
  const env = seeded({ [KEY]: id });
  const h = createBoxHarness({}, { environment: env });
  await h.panel.settle();
  for (let n = 0; n < 7; n++) {
    await inspectNext(h, { status: 'unknown' });
    if (n < 6) { env.runTimers(); await h.panel.settle(); }
  }
  assert.deepEqual(env.timerDelays, []);
  assert.ok(h.panel.byClass('create-box-retry-now'));
  env.dispatch('online');
  await h.panel.settle();
  h.unmount();
  inspections(h).at(-1).resolve({ status: 'created', box: committed });
  await h.panel.settle();
  assert.equal(h.panel.updatesAfterUnmount, 0);
  assert.equal(env.window.sessionStorage.getItem(KEY), id);
});

for (const [label, entries] of [
  ['envelope only, malformed', { [STORAGE]: '{not json' }],
  ['envelope only, invalid UUID', { [STORAGE]: JSON.stringify({ request_id: 'not-a-uuid', strain: 1 }) }],
  ['key only, invalid', { [KEY]: 'garbage' }],
  ['both destroyed', { [STORAGE]: 'null', [KEY]: '' }],
]) test(`no surviving UUID (${label}): honest notice, nothing sent, nothing silently erased`, async () => {
  const env = seeded(entries);
  const h = createBoxHarness({}, { environment: env });
  await ready(h);
  assert.equal(h.mutations.length + h.resolutions.length, 0, 'There is no key the server could settle');
  assert.ok(panelText(h).includes('createBoxPreviousUnverified'));
  assert.doesNotMatch(panelText(h), TECHNICAL_COPY);
  for (const [key, value] of Object.entries(entries)) assert.equal(env.window.sessionStorage.getItem(key), value);
  assert.equal(save(h).disabled, true);
  await send(h);
  assert.equal(h.mutations.length, 0, 'Unknown original identity cannot be replaced safely');
  for (const [key, value] of Object.entries(entries)) assert.equal(env.window.sessionStorage.getItem(key), value);
  h.unmount();
});

test('another actor or institution key record is never read', async () => {
  const env = seeded({
    'polypbase.automaticBox.8.1.requestId': crypto.randomUUID(),
    'polypbase.automaticBox.7.2.requestId': crypto.randomUUID(),
  });
  const h = createBoxHarness({}, { environment: env });
  await ready(h);
  assert.equal(h.mutations.length + h.resolutions.length, 0);
  assert.equal(panelText(h).includes('createBoxPreviousUnverified'), false);
  h.unmount();
});

test('another actor or institution never sees or replays a pending creation', async () => {
  const env = createBoxEnvironment();
  env.window.sessionStorage.setItem('polypbase.automaticBox.8.1', JSON.stringify({ request_id: crypto.randomUUID(), strain: 1, thermal_zone: 10, entered_on: '2026-10-01' }));
  env.window.sessionStorage.setItem('polypbase.automaticBox.7.2', JSON.stringify({ request_id: crypto.randomUUID(), strain: 1, thermal_zone: 10, entered_on: '2026-10-01' }));
  const h = createBoxHarness({}, { environment: env });
  await ready(h);
  assert.equal(h.mutations.length + h.resolutions.length, 0);
  assert.equal(save(h).disabled, false);
  h.unmount();
});

test('switching institutions isolates a pending creation and resumes it on return', async () => {
  const h = await ready(createPilotageHarness());
  const { result } = await send(h);
  const intent = plain(h.mutations[0].payload);
  const original = h.panel;
  h.setParentProps({ activeOrganizationId: 2, profile: { ...h.props.profile, active_organization: { id: 2 }, memberships: [{ organization: { id: 2 }, role: 'admin' }] } });
  assert.equal(field(h, 'date').disabled, false);
  h.mutations[0].reject(failure(h, 'network'));
  await result;
  assert.equal(original.updatesAfterUnmount, 0);
  assert.equal(h.mutations.length, 1);
  assert.deepEqual(h.env.timerDelays, [], 'The old institution schedules nothing after unmount');
  h.setParentProps({ activeOrganizationId: 1, profile: h.props.profile });
  await h.panel.settle();
  assert.equal(h.mutations.length, 2);
  assert.deepEqual(plain(h.mutations[1].payload), intent);
  h.mutations[1].resolve(committed);
  await h.panel.settle();
  assert.deepEqual(h.navigations, []);
  h.unmount();
});

for (const boundary of ['generation', 'unmount']) for (const completion of ['resolve', 'reject']) {
  test(`${boundary} during creation ignores the late ${completion} and schedules nothing`, async () => {
    const h = await ready();
    const { result } = await send(h);
    if (boundary === 'generation') h.invalidateOperation();
    else h.panel.unmount();
    h.mutations[0][completion](completion === 'resolve' ? committed : failure(h, 'network'));
    await result;
    await h.panel.settle();
    assert.deepEqual(h.navigations, []);
    assert.deepEqual(h.env.timerDelays, []);
    assert.equal(h.resolutions.length, 0);
    assert.equal(h.panel.updatesAfterUnmount, 0);
    if (boundary === 'generation') h.panel.unmount();
  });
}

test('session persistence failure prevents the request rather than risking a duplicate on remount', async () => {
  const h = await ready();
  h.env.window.sessionStorage.setItem = () => { throw new Error('Storage denied'); };
  const { result } = await send(h);
  await result;
  h.panel.flush();
  assert.equal(h.mutations.length, 0);
  assert.equal(field(h, 'date').disabled, false);
  assert.ok(panelText(h).includes('createBoxStorageUnavailable'));
  h.unmount();
});

const sourceAst = path => ts.createSourceFile(path, readFileSync(new URL(path, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function nodes(root, predicate) {
  const found = [];
  function visit(node) { if (predicate(node)) found.push(node); ts.forEachChild(node, visit); }
  visit(root);
  return found;
}

test('AST: App creation posts only to automatic endpoint and retains guarded cache publication', () => {
  const ast = sourceAst('../src/App.tsx');
  const create = nodes(ast, n => ts.isFunctionDeclaration(n) && n.name?.text === 'createBox')[0];
  const posts = nodes(create, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'apiPost');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].arguments[0].text, '/api/boxes/automatic/intents/');
  assert.equal(posts[1].arguments[0].text, '/api/boxes/automatic/intents/execute/');
  assert.equal(posts[0].arguments[1].getText(ast), 'payload');
  const body = create.getText(ast);
  assert.match(body, /assertCurrent\(\);[\s\S]*commit\(detail.id\)/);
  assert.match(body, /mergeBoxDetail\(current, detail\)/);
  assert.match(body, /upsertBoxes\(current.boxes, \[detail\]\)/);
  assert.match(body, /await publication.reconcile\(\);/);
});

test('AST: automatic payload type excludes all client code fields and requires UUID/date', () => {
  const ast = sourceAst('../src/types.ts');
  const type = ast.statements.find(n => ts.isTypeAliasDeclaration(n) && n.name.text === 'BoxCreatePayload');
  const fields = new Map(type.type.members.map(n => [n.name.getText(ast), n]));
  assert.deepEqual([...fields.keys()].sort(), ['entered_on', 'notes', 'request_id', 'strain', 'thermal_zone', 'volume_liters']);
  for (const name of ['entered_on', 'request_id', 'strain', 'thermal_zone']) assert.equal(fields.get(name).questionToken, undefined);
  for (const name of ['notes', 'volume_liters']) assert.ok(fields.get(name).questionToken);
  const identity = ast.statements.find(n => ts.isTypeAliasDeclaration(n) && n.name.text === 'BoxStrainIdentity').getText(ast);
  assert.doesNotMatch(identity, /box_number|next|global_code/, 'The identity contract carries no Box number');
});

test('AST: no prediction; persisted UUID precedes the only creation call; unlock only after settlement', () => {
  const ast = sourceAst('../src/components/CreateBoxPanel.tsx');
  const source = ast.getFullText();
  assert.doesNotMatch(source, /buildNextBoxCode|boxCodeMatchesBoxNumber|onRequestBoxCollection|isBoxCollectionReady|boxNumber|confirmAction/);
  const submit = nodes(ast, n => ts.isFunctionDeclaration(n) && n.name?.text === 'handleSubmit')[0].getText(ast);
  const store = submit.indexOf('storeCreationIntent(');
  assert.ok(store >= 0 && store < submit.indexOf('await reconcile()'));
  assert.match(submit, /const storedSuccessfully = await storeCreationIntent\(storageKey!, intent\)/);
  assert.match(submit, /if \(!storedSuccessfully\) \{[\s\S]*?return;/);
  assert.match(submit, /request_id: window.crypto.randomUUID\(\)/);
  assert.doesNotMatch(submit, /onCreateBox|onResolveBoxCreation/);
  const reconcile = nodes(ast, n => ts.isFunctionDeclaration(n) && n.name?.text === 'reconcile')[0];
  assert.equal(nodes(reconcile, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'onCreateBox').length, 1);
  assert.equal(nodes(reconcile, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'onResolveBoxCreation').length, 0);
  assert.doesNotMatch(reconcile.getText(ast), /randomUUID/);
  assert.equal(nodes(ast, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'window.crypto.randomUUID').length, 2);
  // Only a created Box or a durable cancellation clears the stored key.
  const settleCallers = nodes(ast, n => ts.isFunctionDeclaration(n) && n.name?.text !== 'CreateBoxPanel'
    && /settle\(\);/.test(n.body?.getText(ast) ?? '')).map(n => n.name.text).sort();
  assert.deepEqual(settleCallers, ['finishCancelled', 'finishCreated']);
  const helper = sourceAst('../src/utils/boxCreationRecovery.ts').getFullText();
  assert.match(helper, /CREATION_RETRY_DELAYS_MS = \[1000, 2000, 4000, 8000, 15000, 30000\]/);
});

for (const status of ['created', 'cancelled']) test(`App resolution ${status} uses guarded endpoint and normal cache publication`, async () => {
  const h = appHarness();
  const before = h.state.data;
  const pending = h.context.resolveBoxCreation('request-uuid');
  assert.equal(h.requests[0].url, '/api/boxes/automatic/resolve/');
  assert.deepEqual(plain(h.requests[0].payload), { request_id: 'request-uuid' });
  const response = status === 'created' ? { status, box: committed } : { status };
  h.requests[0].resolve(response);
  assert.deepEqual(plain(await pending), response);
  if (status === 'created') {
    assert.equal(h.state.data.boxDetails[42].global_code, committed.global_code);
    assert.equal(h.state.data.boxes[0].global_code, committed.global_code);
    assert.equal(h.state.data.overview, null);
    assert.equal(h.state.data.exportOptions, null);
  } else assert.equal(h.state.data, before);
});

test('App resolution rejects old institution result before publication', async () => {
  const h = appHarness();
  const before = h.state.data;
  const pending = h.context.resolveBoxCreation('request-uuid');
  const rejected = assert.rejects(pending);
  h.switchOrganization();
  h.requests[0].resolve({ status: 'created', box: committed });
  await rejected;
  assert.equal(h.state.data, before);
});

test('AST: App resolution keeps the guarded publication', () => {
  const app = sourceAst('../src/App.tsx');
  const callback = nodes(app, n => ts.isFunctionDeclaration(n) && n.name?.text === 'resolveBoxCreation')[0].getText(app);
  assert.match(callback, /getOperationRequests\(operationGeneration\)/);
  assert.match(callback, /assertCurrent\(\);\s*if \(result.status === 'created'\)/);
  assert.match(callback, /beginBoxMutation\(true\)/);
  assert.match(callback, /mergeBoxDetail\(current, detail\)/);
  assert.match(callback, /await publication.reconcile\(\);\s*assertCurrent\(\)/);
});

test('catalogs no longer contain researcher-facing request recovery copy', () => {
  for (const language of ['fr', 'en']) {
    const catalog = readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8');
    assert.doesNotMatch(catalog, /createBoxPendingIntent|createBoxResolve|createBoxRequestCancelled|createBoxRequestConflict|confirmCreateBox/);
    assert.doesNotMatch(catalog, /Résoudre la demande|Resolve request|Création à confirmer|verrouillés pour éviter un doublon/);
  }
});

for (const language of ['fr', 'en']) test(`${language}: creation controls, connection notice and refusal are translated`, async () => {
  const env = createBoxEnvironment();
  const t = env.load('../src/i18n/index.ts').createTranslator(language);
  const h = createBoxHarness({ t }, { environment: env });
  assert.equal(text(h.panel.nodes.find(n => n.type === 'h2')), language === 'fr' ? 'Nouvelle boîte' : 'New box');
  await ready(h);
  assert.equal(text(save(h)), language === 'fr' ? 'Créer la boîte' : 'Create box');
  assert.equal(h.panel.byClass('created-box-identifier'), undefined);
  assert.equal(h.env.requests.length, 1, 'The form does not request an identifier preview');
  const { result } = await send(h);
  h.mutations[0].reject(failure(h, 'network'));
  await result;
  h.panel.flush();
  assert.ok(panelText(h).includes(t('createBoxConnectionPending')));
  h.env.runTimers();
  await h.panel.settle();
  h.mutations[1].reject(new h.api.ApiError(403, 'Raw refusal'));
  await h.panel.settle();
  assert.equal(h.resolutions.length, 0);
  assert.ok(panelText(h).includes(language === 'fr' ? 'Ce compte ne peut pas créer de boîte.' : 'This account cannot create boxes.'));
  h.unmount();
});

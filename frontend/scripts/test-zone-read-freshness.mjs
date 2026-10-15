import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, deferred, tick } from './app-operation-test-harness.mjs';

test('same-zone reverse temperature responses cannot replace newer zero with stale twelve', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null)];
  const first = h.context.recordManualTemperature(1, {});
  const older = h.requests.at(-1);
  const second = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await second;
  older.resolve(zone(1, 12));
  await tick();
  assert.equal(h.state.data.zones[0].latest_temperature, 0, 'a delayed mutation snapshot must not overwrite zero');
  const read = h.requests.at(-1);
  assert.equal(read.method, 'apiGet');
  assert.equal(read.url, '/api/thermal-zones/?limit=80');
  read.resolve(page([zone(1, 0)]));
  await first;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
});

const path = '/api/thermal-zones/?limit=80';
const next = '/api/thermal-zones/?limit=80&offset=80';
const page = (results, next = null) => ({ results, next });
const zone = (id, value) => ({ id, name: `Zone ${id}`, latest_temperature: value, latest_salinity: null });

async function startRefresh(h) {
  const pending = h.context.createProbe({ thermal_zone: 1 });
  h.requests.at(-1).resolve({ id: 10 });
  await tick();
  return { pending, first: h.requests.at(-1) };
}

test('delayed old page 2 cannot overwrite a newer authoritative zero', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null), zone(2, null)];
  const old = await startRefresh(h);
  old.first.resolve(page([zone(1, 12)], next));
  await tick();
  const second = h.requests.at(-1);
  const mutation = h.context.recordManualTemperature(1, { temperature_c: 0 });
  h.requests.at(-1).resolve(zone(1, 0));
  await mutation;
  second.resolve(page([zone(2, 15)]));
  await old.pending;
  assert.equal(h.state.data.zones.find(item => item.id === 1).latest_temperature, 0);
  assert.equal(h.state.recovery, null, 'obsolete completion is not a refresh failure');
});

for (const outcome of ['success', 'failure']) {
  test(`older complete refresh finishing last (${outcome}) is a safe no-op`, async () => {
    const h = appHarness();
    const older = await startRefresh(h);
    older.first.resolve(page([zone(1, 12)], next));
    await tick();
    const laterPage = h.requests.at(-1);
    const newer = await startRefresh(h);
    newer.first.resolve(page([zone(1, 0)], next));
    await tick();
    h.requests.at(-1).resolve(page([zone(1, 99), zone(2, null)]));
    await newer.pending;
    const authoritative = h.state.data;
    if (outcome === 'success') laterPage.resolve(page([zone(2, 14)]));
    else laterPage.reject(new Error('Obsolete page failed'));
    await older.pending;
    assert.equal(h.state.data, authoritative);
    assert.deepEqual(Array.from(h.state.data.zones, item => [item.id, item.latest_temperature]), [[1, 0], [2, null]]);
    assert.equal(h.state.recovery, null);
  });
}

for (const boundary of ['mutation', 'collection']) {
  test(`deferred React collection publication rechecks a newer ${boundary} boundary`, async () => {
    const h = appHarness();
    const old = await startRefresh(h);
    h.queueUpdates();
    old.first.resolve(page([zone(1, 12)]));
    await old.pending;
    const queued = h.queuedUpdates[0];
    if (boundary === 'mutation') {
      const pending = h.context.recordManualSalinity(1, { salinity_psu: 0 });
      h.requests.at(-1).resolve({ ...zone(1, null), salinity_psu: '0' });
      await pending;
    } else {
      const newer = await startRefresh(h);
      newer.first.resolve(page([zone(1, 0)]));
      await newer.pending;
    }
    const replacement = { ...h.state.data, zones: [zone(1, 0)] };
    assert.equal(queued(replacement), replacement);
  });
}

test('targeted capability update preserves membership and invalidates an older pending full refresh', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null), zone(2, 0)];
  const old = await startRefresh(h);
  old.first.resolve(page([zone(1, 12)], next));
  await tick();
  const second = h.requests.at(-1);
  const pending = h.context.refreshZoneSalinityCapability(1);
  const reading = { id: 5, salinity_psu: '0', can_edit: false };
  h.requests.at(-1).resolve([reading]);
  await pending;
  second.resolve(page([zone(2, 12)]));
  await old.pending;
  assert.equal(h.state.data.zones.length, 2);
  assert.equal(h.state.data.zones[0].latest_salinity, reading);
  assert.equal(h.state.data.zones[0].latest_temperature, null);
  assert.equal(h.state.data.zones[1].latest_temperature, 0);
  assert.equal(h.state.recovery, null);
});

test('targeted updates to independent zones survive a later collection start and each other', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null), zone(2, null)];
  h.queueUpdates();
  const first = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await first;
  const refresh = h.context.refreshZones();
  const collection = h.requests.at(-1);
  const second = h.context.recordManualTemperature(2, {});
  h.requests.at(-1).resolve(zone(2, 0));
  await second;
  collection.resolve(page([zone(1, 12), zone(2, 12)]));
  await refresh;
  for (const update of h.queuedUpdates) h.state.data = update(h.state.data);
  assert.deepEqual(Array.from(h.state.data.zones, item => item.latest_temperature), [0, 0]);
});

for (const deferredPublication of [false, true]) {
  test(`older targeted capability cannot undo a salinity correction${deferredPublication ? ' with deferred publication' : ''}`, async () => {
    const h = appHarness();
    h.state.data.zones = [zone(1, null), zone(2, null)];
    const pending = h.context.refreshZoneSalinityCapability(1);
    const history = h.requests.at(-1);
    if (deferredPublication) {
      h.queueUpdates();
      history.resolve([{ id: 4, salinity_psu: '32' }]);
      await pending;
    }
    const mutation = h.context.updateManualSalinity(1, 4, { salinity_psu: 0 });
    h.requests.at(-1).resolve({ ...zone(1, null), latest_salinity: { id: 4, salinity_psu: '0' } });
    await mutation;
    if (!deferredPublication) { history.resolve([]); await pending; }
    for (const update of h.queuedUpdates) h.state.data = update(h.state.data);
    assert.equal(h.state.data.zones.find(item => item.id === 1).latest_salinity.salinity_psu, '0');
    assert.equal(h.state.data.zones.length, 2);
  });
}

test('partial capability publication does not cancel a queued temperature payload for the same zone', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null), zone(2, null)];
  h.queueUpdates();
  const temperature = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await temperature;
  const capability = h.context.refreshZoneSalinityCapability(1);
  h.requests.at(-1).resolve([{ id: 4, salinity_psu: '0' }]);
  await capability;
  for (const update of h.queuedUpdates) h.state.data = update(h.state.data);
  const updated = h.state.data.zones.find(item => item.id === 1);
  assert.equal(updated.latest_temperature, 0);
  assert.equal(updated.latest_salinity.salinity_psu, '0');
  assert.equal(h.state.data.zones.length, 2);
});

for (const order of ['reverse responses', 'deferred publication']) {
  test(`capability reads use latest-start order with ${order}`, async () => {
    const h = appHarness();
    h.state.data.zones = [zone(1, null)];
    const first = h.context.refreshZoneSalinityCapability(1);
    const request = h.requests.at(-1);
    if (order === 'deferred publication') {
      h.queueUpdates();
      request.resolve([{ salinity_psu: '32' }]);
      await first;
    }
    const second = h.context.refreshZoneSalinityCapability(1);
    h.requests.at(-1).resolve([{ salinity_psu: '0' }]);
    await second;
    if (order === 'reverse responses') { request.resolve([]); await first; }
    for (const update of h.queuedUpdates) h.state.data = update(h.state.data);
    assert.equal(h.state.data.zones[0].latest_salinity.salinity_psu, '0');
  });
}

test('empty capability history remains null rather than a scientific zero', async () => {
  const h = appHarness();
  h.state.data.zones = [{ ...zone(1, 0), latest_salinity: { salinity_psu: '0' } }];
  const pending = h.context.refreshZoneSalinityCapability(1);
  h.requests.at(-1).resolve([]);
  await pending;
  assert.equal(h.state.data.zones[0].latest_salinity, null);
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
});

test('unrelated Box detail boundaries do not cancel movement zone pagination', async () => {
  const h = appHarness();
  const pending = h.context.refreshMovementState(7);
  const detail = h.requests.find(item => item.url === '/api/boxes/7/');
  const zones = h.requests.find(item => item.url === '/api/thermal-zones/');
  zones.resolve(page([zone(1, 0)], next));
  await tick();
  h.context.invalidateBoxDetail(7);
  h.requests.at(-1).resolve(page([zone(2, null)]));
  await tick();
  assert.equal(h.state.data.zones.length, 2, 'zone publication is independent of the Box detail result');
  const rejected = assert.rejects(pending, error => error.constructor.name === 'BoxDetailSupersededError');
  detail.resolve({ id: 7 });
  await rejected;
});

for (const operation of [
  { name: 'zone creation', run: h => h.context.createThermalZone({}) },
  { name: 'zone update', run: h => h.context.updateThermalZone(1, {}) },
  { name: 'probe creation', run: h => h.context.createProbe({ thermal_zone: 1 }) },
  { name: 'movement', run: h => h.context.moveBox(7, {}) },
  { name: 'initial location', run: h => h.context.assignBoxInitialLocation(7, {}) },
]) {
  test(`${operation.name} invalidates an old queued collection before its own refresh finishes`, async () => {
    const h = appHarness();
    const old = h.context.refreshZones();
    h.queueUpdates();
    h.requests.at(-1).resolve(page([zone(1, 12)]));
    await old;
    const queued = h.queuedUpdates.shift();
    const mutation = operation.run(h);
    h.requests.at(-1).resolve({ id: 7 });
    await tick();
    const current = { ...h.state.data, zones: [zone(1, 0)] };
    assert.equal(queued(current), current);
    assert.equal(h.requests.at(-1).url, path);
    h.requests.at(-1).resolve(page([zone(1, 0)]));
    await mutation;
  });
}

for (const deferredPublication of [false, true]) {
  test(`older capability read cannot undo a newer complete collection${deferredPublication ? ' after queuing' : ''}`, async () => {
    const h = appHarness();
    h.state.data.zones = [zone(1, null)];
    const pending = h.context.refreshZoneSalinityCapability(1);
    const history = h.requests.at(-1);
    if (deferredPublication) {
      h.queueUpdates();
      history.resolve([{ salinity_psu: '32' }]);
      await pending;
    }
    const refresh = h.context.refreshZones();
    h.requests.at(-1).resolve(page([{ ...zone(1, 0), latest_salinity: { salinity_psu: '0' } }]));
    await refresh;
    if (!deferredPublication) { history.resolve([]); await pending; }
    for (const update of h.queuedUpdates) h.state.data = update(h.state.data);
    assert.equal(h.state.data.zones[0].latest_salinity.salinity_psu, '0');
  });
}

for (const operation of [
  { name: 'salinity creation', run: h => h.context.recordManualSalinity(1, {}) },
  { name: 'salinity correction', run: h => h.context.updateManualSalinity(1, 4, {}) },
]) {
  test(`same-zone reverse ${operation.name} responses reconcile rather than replace zero`, async () => {
    const h = appHarness();
    h.state.data.zones = [zone(1, null), zone(2, 8)];
    const first = operation.run(h);
    const older = h.requests.at(-1);
    const second = operation.run(h);
    const zero = { ...zone(1, null), latest_salinity: { id: 4, salinity_psu: '0' } };
    h.requests.at(-1).resolve(zero);
    await second;
    older.resolve({ ...zero, latest_salinity: { id: 4, salinity_psu: '32' } });
    await tick();
    assert.equal(h.state.data.zones[0].latest_salinity.salinity_psu, '0');
    assert.equal(h.requests.at(-1).method, 'apiGet');
    h.requests.at(-1).resolve(page([zero]));
    await first;
    assert.equal(h.state.data.zones[0].latest_salinity.salinity_psu, '0');
    assert.equal(h.state.data.zones[1].latest_temperature, 8);
  });
}

test('thermal-zone PATCH snapshots use fresh complete summaries after reverse responses', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null)];
  const first = h.context.updateThermalZone(1, {});
  const older = h.requests.at(-1);
  const second = h.context.updateThermalZone(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await tick();
  h.requests.at(-1).resolve(page([zone(1, 0)]));
  await second;
  older.resolve(zone(1, 12));
  await tick();
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.requests.at(-1).method, 'apiGet');
  h.requests.at(-1).resolve(page([zone(1, 0)]));
  await first;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
});

test('a thermal-zone update boundary also supersedes an older temperature snapshot', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null)];
  const temperature = h.context.recordManualTemperature(1, {});
  const delayed = h.requests.at(-1);
  const update = h.context.updateThermalZone(1, {});
  h.requests.at(-1).resolve({ id: 1, name: 'Renamed', target_temperature_c: '0' });
  await tick();
  const summary = { ...zone(1, 0), name: 'Renamed', target_temperature_c: '0' };
  h.requests.at(-1).resolve(page([summary]));
  await update;
  delayed.resolve(zone(1, 12));
  await tick();
  assert.equal(h.state.data.zones[0].name, 'Renamed');
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.requests.at(-1).method, 'apiGet');
  h.requests.at(-1).resolve(page([summary]));
  await temperature;
  assert.equal(h.state.data.zones[0], summary);
});

test('concurrent mutations on different zones both publish without reconciliation', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null), zone(2, null)];
  const first = h.context.recordManualTemperature(1, {});
  const older = h.requests.at(-1);
  const second = h.context.recordManualSalinity(2, {});
  h.requests.at(-1).resolve({ ...zone(2, null), latest_salinity: { salinity_psu: '0' } });
  await second;
  older.resolve(zone(1, 0));
  await first;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.state.data.zones[1].latest_salinity.salinity_psu, '0');
  assert.equal(h.requests.length, 2);
});

for (const order of ['temperature first', 'salinity first']) {
  test(`same-zone different-field snapshots reconcile both changes (${order})`, async () => {
    const h = appHarness();
    h.state.data.zones = [zone(1, null), zone(2, 8)];
    const temperature = h.context.recordManualTemperature(1, {});
    const temperatureRequest = h.requests.at(-1);
    const salinity = h.context.recordManualSalinity(1, {});
    const salinityRequest = h.requests.at(-1);
    const first = order === 'temperature first' ? temperature : salinity;
    const last = order === 'temperature first' ? salinity : temperature;
    if (order === 'temperature first') temperatureRequest.resolve(zone(1, 0));
    else salinityRequest.resolve({ ...zone(1, null), latest_salinity: { salinity_psu: '0' } });
    await first;
    if (order === 'temperature first') salinityRequest.resolve({ ...zone(1, null), latest_salinity: { salinity_psu: '0' } });
    else temperatureRequest.resolve(zone(1, 0));
    await tick();
    assert.equal(h.requests.at(-1).method, 'apiGet');
    assert.equal(h.state.data.overview, null, 'successful temperature writes still invalidate their derived cache');
    assert.equal(h.state.data.exportOptions, null);
    const authoritative = { ...zone(1, 0), latest_salinity: { salinity_psu: '0' } };
    h.requests.at(-1).resolve(page([zone(2, 99)], next));
    await tick();
    assert.equal(h.state.data.zones[1].latest_temperature, 8, 'lookup does not publish unrelated summaries');
    h.requests.at(-1).resolve(page([authoritative]));
    await last;
    assert.equal(h.state.data.zones[0].latest_temperature, 0);
    assert.equal(h.state.data.zones[0].latest_salinity.salinity_psu, '0');
    assert.equal(h.state.data.zones[1].latest_temperature, 8);
  });
}

for (const failureOrder of ['before earlier success', 'after earlier success']) {
  test(`a later failed mutation does not suppress earlier success (${failureOrder})`, async () => {
    const h = appHarness();
    h.state.data.zones = [zone(1, null)];
    const first = h.context.recordManualTemperature(1, {});
    const older = h.requests.at(-1);
    const second = h.context.recordManualTemperature(1, {});
    const failed = h.requests.at(-1);
    const failure = new Error('Write rejected');
    const rejected = assert.rejects(second, error => error === failure);
    if (failureOrder === 'before earlier success') { failed.reject(failure); await rejected; }
    older.resolve(zone(1, 0));
    await first;
    if (failureOrder === 'after earlier success') { failed.reject(failure); await rejected; }
    assert.equal(h.state.data.zones[0].latest_temperature, 0);
    assert.equal(h.requests.length, 2, 'failed dispatch does not allocate a successful mutation boundary');
    assert.equal(h.state.recovery, null);
  });
}

test('deferred mutation and reconciliation updaters cannot cross a newer successful zone boundary', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null)];
  h.queueUpdates();
  const first = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 12));
  await first;
  const stale = h.queuedUpdates.shift();
  const second = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await second;
  const current = { ...h.state.data, zones: [zone(1, 0)] };
  assert.equal(stale(current), current);
  // Force a response that overlapped the successful second boundary.
  const third = h.context.recordManualTemperature(1, {});
  const delayed = h.requests.at(-1);
  const fourth = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await fourth;
  delayed.resolve(zone(1, 12));
  await tick();
  h.requests.at(-1).resolve(page([zone(1, 12)]));
  await third;
  const reconciled = h.queuedUpdates.at(-1);
  const fifth = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await fifth;
  assert.equal(reconciled(current), current, 'queued GET summary also rechecks the same-zone boundary');
});

test('a fresh server summary, not dispatch or response order, decides ambiguous committed state', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null)];
  const first = h.context.recordManualTemperature(1, {});
  const older = h.requests.at(-1);
  const second = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await second;
  older.resolve(zone(1, 12));
  await tick();
  assert.equal(h.state.data.zones[0].latest_temperature, 0, 'old mutation response is not authoritative');
  h.requests.at(-1).resolve(page([zone(1, 7)]));
  await first;
  assert.equal(h.state.data.zones[0].latest_temperature, 7, 'fresh GET can disagree with both mutation snapshots');
});

test('ambiguous snapshot read failure offers GET-only recovery and preserves zero, null and missing', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null), zone(2, null)];
  const first = h.context.recordManualTemperature(1, {});
  const older = h.requests.at(-1);
  const second = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await second;
  older.resolve(zone(1, 12));
  await tick();
  h.requests.at(-1).reject(new Error('Summary unavailable'));
  await first;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.state.data.zones[0].latest_salinity, null);
  assert.ok(h.state.recovery);
  const writes = h.requests.filter(item => item.method !== 'apiGet').length;
  const recovery = h.state.recovery();
  const summary = { id: 1, name: 'Zone 1', latest_salinity: null, capacity: 0 };
  h.requests.at(-1).resolve(page([summary]));
  await recovery;
  assert.equal(h.state.data.zones[0].capacity, 0);
  assert.equal(h.state.data.zones[0].latest_salinity, null);
  assert.equal(Object.hasOwn(h.state.data.zones[0], 'latest_temperature'), false);
  assert.equal(h.state.data.zones.length, 2);
  assert.equal(h.requests.filter(item => item.method !== 'apiGet').length, writes);
  assert.equal(h.state.recovery, null);
});

test('a capability update completed during a temperature mutation requires a fresh summary', async () => {
  const h = appHarness();
  h.state.data.zones = [zone(1, null)];
  const temperature = h.context.recordManualTemperature(1, {});
  const delayed = h.requests.at(-1);
  const capability = h.context.refreshZoneSalinityCapability(1);
  const reading = { salinity_psu: '0', can_edit: false };
  h.requests.at(-1).resolve([reading]);
  await capability;
  delayed.resolve(zone(1, 0));
  await tick();
  assert.equal(h.state.data.zones[0].latest_salinity, reading);
  h.requests.at(-1).resolve(page([{ ...zone(1, 0), latest_salinity: reading }]));
  await temperature;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.state.data.zones[0].latest_salinity, reading);
});

async function ambiguousTemperature(h) {
  h.state.data.zones = [zone(1, null)];
  const first = h.context.recordManualTemperature(1, {});
  const older = h.requests.at(-1);
  const second = h.context.recordManualTemperature(1, {});
  h.requests.at(-1).resolve(zone(1, 0));
  await second;
  older.resolve(zone(1, 12));
  await tick();
  assert.equal(h.requests.at(-1).method, 'apiGet');
  return { pending: first, read: h.requests.at(-1) };
}

test('a newer same-zone success supersedes pending reconciliation without offering obsolete recovery', async () => {
  const h = appHarness();
  const old = await ambiguousTemperature(h);
  const newer = h.context.recordManualSalinity(1, {});
  const summary = { ...zone(1, 0), latest_salinity: { salinity_psu: '0' } };
  h.requests.at(-1).resolve(summary);
  await newer;
  old.read.reject(new Error('Obsolete summary failed'));
  await old.pending;
  assert.equal(h.state.data.zones[0], summary);
  assert.equal(h.state.recovery, null);
});

for (const deferredPublication of [false, true]) {
  test(`newer capability survives an ambiguous full summary${deferredPublication ? ' queued in React' : ' in flight'}`, async () => {
    const h = appHarness();
    const old = await ambiguousTemperature(h);
    if (deferredPublication) {
      h.queueUpdates();
      old.read.resolve(page([zone(1, 12)]));
      await old.pending;
    }
    const capability = h.context.refreshZoneSalinityCapability(1);
    const reading = { salinity_psu: '0', can_edit: false };
    h.requests.at(-1).resolve([reading]);
    await capability;
    if (!deferredPublication) { old.read.resolve(page([zone(1, 12)])); await old.pending; }
    for (const update of h.queuedUpdates) h.state.data = update(h.state.data);
    assert.equal(h.state.data.zones[0].latest_temperature, 0);
    assert.equal(h.state.data.zones[0].latest_salinity, reading);
  });
}

test('organization replacement cancels pending same-zone reconciliation and cannot install recovery', async () => {
  const h = appHarness();
  const old = await ambiguousTemperature(h);
  const cancelled = assert.rejects(old.pending, h.context.ApiResourceCancelledError);
  h.switchOrganization();
  const replacement = { zones: [zone(201, 0)], boxes: [], boxDetails: {} };
  h.state.data = replacement;
  old.read.resolve(page([zone(1, 12)], next));
  await cancelled;
  assert.equal(h.requests.length, 3, 'no next page can be requested in the replacement institution');
  assert.equal(h.state.data, replacement);
  assert.equal(h.state.recovery, null);
});

test('a complete collection started later owns publication over queued targeted reconciliation', async () => {
  const h = appHarness();
  const old = await ambiguousTemperature(h);
  h.queueUpdates();
  old.read.resolve(page([zone(1, 12)]));
  await old.pending;
  const obsolete = h.queuedUpdates.at(-1);
  const fresh = h.context.refreshZones();
  h.requests.at(-1).resolve(page([zone(1, 0), zone(2, null)]));
  await fresh;
  const current = { ...h.state.data, zones: [zone(1, 0), zone(2, null)] };
  assert.equal(obsolete(current), current);
});

test('actual API client reconciles reverse mutation responses with an unshared summary GET', async () => {
  const h = transportHarness();
  h.state.data.zones = [zone(1, null)];
  const first = h.context.recordManualTemperature(1, {});
  const older = h.fetches.at(-1);
  const second = h.context.recordManualTemperature(1, {});
  h.fetches.at(-1).respond(zone(1, 0));
  await second;
  older.respond(zone(1, 12));
  await tick();
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.fetches.at(-1).path, path);
  assert.equal(h.fetches.at(-1).options.cache, 'no-store');
  assert.equal(h.fetches.at(-1).options.headers.get('X-Organization-Id'), '1');
  h.fetches.at(-1).respond(page([zone(1, 0)]));
  await first;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
  assert.equal(h.fetches.filter(item => item.options.method === 'POST').length, 2);
});

const clientCode = ts.transpileModule(readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function transportHarness() {
  const h = appHarness();
  const fetches = [];
  const storage = new Map();
  Object.assign(h.context, {
    Headers, AbortController, document: { cookie: '' },
    fetch(path, options) {
      const pending = deferred();
      fetches.push({ path, options, ...pending,
        respond(body) { pending.resolve({ ok: true, headers: new Headers({ 'content-type': 'application/json' }), json: async () => body }); },
      });
      return pending.promise;
    },
  });
  h.context.window.localStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key),
  };
  const client = vm.runInContext(`(() => { const exports = {}; ${clientCode}\nreturn exports; })()`, h.context);
  Object.assign(h.context, client);
  client.setActiveOrganizationContext(1);
  return { ...h, client, fetches };
}

test('actual API client does not share either page across overlapping zone refreshes', async () => {
  const h = transportHarness();
  const old = h.context.refreshZones();
  const fresh = h.context.refreshZones();
  assert.equal(h.fetches.length, 2, 'even identical first-page paths must not share a snapshot');
  h.fetches[0].respond(page([zone(1, 12)], next));
  h.fetches[1].respond(page([zone(1, 0)], next));
  await tick();
  assert.equal(h.fetches.length, 4, 'later pages must not share either');
  assert.ok(h.fetches.every(item => item.options.cache === 'no-store'));
  h.fetches[3].respond(page([zone(2, null)]));
  await fresh;
  h.fetches[2].respond(page([zone(2, 12)]));
  await old;
  assert.deepEqual(Array.from(h.state.data.zones, item => item.latest_temperature), [0, null]);
});

test('actual client keeps pending A pagination out of B, including a return to A', async () => {
  const h = transportHarness();
  const old = h.context.refreshZones();
  const rejected = assert.rejects(old, h.context.ApiResourceCancelledError);
  h.fetches[0].respond(page([zone(1, 12)], next));
  await tick();
  h.switchOrganization();
  h.context.operationGeneration = 1;
  h.client.setActiveOrganizationContext(2);
  const fresh = h.context.refreshZones();
  assert.equal(h.fetches.at(-1).options.headers.get('X-Organization-Id'), '2');
  h.fetches.at(-1).respond(page([zone(201, 0)]));
  await fresh;
  h.fetches[1].respond(page([zone(2, 12)], '/api/thermal-zones/?offset=160'));
  await rejected;
  assert.equal(h.fetches.length, 3);
  assert.equal(h.state.data.zones[0].id, 201);
  h.switchOrganization();
  h.context.operationGeneration = 2;
  h.client.setActiveOrganizationContext(1);
  const back = h.context.refreshZones();
  assert.equal(h.fetches.at(-1).options.headers.get('X-Organization-Id'), '1');
  h.fetches.at(-1).respond(page([zone(1, 0)]));
  await back;
  assert.equal(h.state.data.zones[0].latest_temperature, 0);
});
